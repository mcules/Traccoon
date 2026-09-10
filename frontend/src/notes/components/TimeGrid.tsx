import { useEffect, useRef, type CSSProperties } from 'react';
import { tr } from "../../i18n";
import type { CalEvent } from '../lib/api';
import type { EventDraft } from './EventDialog';
import { hueFor } from '../lib/calendarColour';

/**
 * Day and week as a clock, not as a list.
 *
 * A list says what is on; a grid says when, how long, and what collides — which
 * is the question one actually has when looking at a week. Hours run down the
 * side, each appointment is a block at its own time, and overlapping ones share
 * the width instead of hiding each other.
 *
 * All-day entries sit in a strip above the grid: they have no place on a clock,
 * and stretching them over 24 hours would bury everything else.
 */

const HOUR_HEIGHT = 44;
const pad = (n: number) => String(n).padStart(2, '0');
const iso = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const WEEKDAYS = ['Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So'];


const minutesOf = (t: string) => {
  const [h, m] = t.split(':').map(Number);
  return (h || 0) * 60 + (m || 0);
};

interface Placed {
  event: CalEvent;
  top: number;
  height: number;
  /** Which of the overlapping columns this one takes, and how many there are. */
  column: number;
  columns: number;
}

/**
 * Lay the day's timed events out. Events that overlap in time form a cluster and
 * split the width between them, so nothing is hidden behind anything else.
 */
function layout(events: CalEvent[]): Placed[] {
  const timed = events
    .filter((e) => !e.allDay)
    .map((e) => {
      const start = minutesOf(e.time || '00:00');
      const end = Math.max(minutesOf(e.endTime || e.time || '00:00'), start + 30);
      return { event: e, start, end };
    })
    .sort((a, b) => a.start - b.start || a.end - b.end);

  const out: Placed[] = [];
  let cluster: typeof timed = [];
  let clusterEnd = -1;

  const flush = () => {
    if (!cluster.length) return;
    // Within a cluster, give each event the first column that is free.
    const columnEnds: number[] = [];
    const assigned = cluster.map((item) => {
      let col = columnEnds.findIndex((end) => end <= item.start);
      if (col < 0) {
        col = columnEnds.length;
        columnEnds.push(item.end);
      } else columnEnds[col] = item.end;
      return { item, col };
    });
    for (const { item, col } of assigned) {
      out.push({
        event: item.event,
        top: (item.start / 60) * HOUR_HEIGHT,
        height: Math.max(((item.end - item.start) / 60) * HOUR_HEIGHT, 22),
        column: col,
        columns: columnEnds.length,
      });
    }
    cluster = [];
    clusterEnd = -1;
  };

  for (const item of timed) {
    if (cluster.length && item.start >= clusterEnd) flush();
    cluster.push(item);
    clusterEnd = Math.max(clusterEnd, item.end);
  }
  flush();
  return out;
}

export default function TimeGrid({
  days,
  byDay,
  today,
  onOpen,
  onNote,
  onNew,
}: {
  days: Date[];
  byDay: Map<string, CalEvent[]>;
  today: string;
  onOpen: (d: EventDraft) => void;
  onNote: (date: string) => void;
  onNew: (date: string, hour: number) => void;
}) {
  const scroller = useRef<HTMLDivElement>(null);

  // Open on the working day rather than at midnight — the first hours are
  // almost always empty and would be all one sees.
  useEffect(() => {
    if (scroller.current) scroller.current.scrollTop = 7 * HOUR_HEIGHT;
  }, [days[0]?.toDateString()]);

  const hasAllDay = days.some((d) => (byDay.get(iso(d)) ?? []).some((e) => e.allDay));

  // One column template for all three rows, driven by how many days are shown.
  const style = { ['--tg-days' as string]: String(days.length) } as CSSProperties;

  return (
    <div className="timegrid" style={style}>
      <div className="tg-head" style={style}>
        <div className="tg-gutter" />
        {days.map((d) => {
          const key = iso(d);
          return (
            <div key={key} className={`tg-day${key === today ? ' today' : ''}`}>
              <button className="tg-daylabel" onClick={() => onNote(key)} title={tr("notes_calendar.open_daily_note")}>
                <span className="tg-weekday">{WEEKDAYS[(d.getDay() + 6) % 7]}</span>
                <span className="tg-daynum">{d.getDate()}.{d.getMonth() + 1}.</span>
              </button>
            </div>
          );
        })}
      </div>

      {hasAllDay && (
        <div className="tg-allday" style={style}>
          <div className="tg-gutter">{tr("notes_calendar.all_day")}</div>
          {days.map((d) => (
            <div key={iso(d)} className="tg-day">
              {(byDay.get(iso(d)) ?? [])
                .filter((e) => e.allDay)
                .map((e) => (
                  <div
                    key={e.id}
                    className={`calendar-event${e.cancelled ? ' cancelled' : ''}`}
                    style={{ borderLeftColor: `hsl(${hueFor(e.calendar)}, 60%, 50%)` }}
                    title={`${e.title} · ${e.calendar}`}
                    onClick={() => onOpen({ uid: e.uid, calendarName: e.calendar, title: e.title, date: e.date, time: '', endTime: '', allDay: true, location: e.location, description: e.description })}
                  >
                    <span className="calendar-event-title">{e.title}</span>
                  </div>
                ))}
            </div>
          ))}
        </div>
      )}

      <div className="tg-body" ref={scroller} style={style}>
        <div className="tg-gutter">
          {Array.from({ length: 24 }, (_, h) => (
            <div key={h} className="tg-hour" style={{ height: HOUR_HEIGHT }}>
              <span>{pad(h)}:00</span>
            </div>
          ))}
        </div>
        {days.map((d) => {
          const key = iso(d);
          const placed = layout(byDay.get(key) ?? []);
          const now = new Date();
          const isToday = key === today;
          return (
            <div key={key} className={`tg-day tg-column${isToday ? ' today' : ''}`}>
              {Array.from({ length: 24 }, (_, h) => (
                <div
                  key={h}
                  className="tg-slot"
                  style={{ height: HOUR_HEIGHT }}
                  onDoubleClick={() => onNew(key, h)}
                  title={tr("notes_calendar.double_click_new")}
                />
              ))}
              {isToday && (
                <div
                  className="tg-now"
                  style={{ top: ((now.getHours() * 60 + now.getMinutes()) / 60) * HOUR_HEIGHT }}
                />
              )}
              {placed.map((p) => (
                <div
                  key={p.event.id}
                  className={`tg-event${p.event.cancelled ? ' cancelled' : ''}${p.height < 34 ? ' tg-short' : ''}`}
                  style={{
                    top: p.top,
                    height: p.height,
                    left: `calc(${(p.column / p.columns) * 100}% + 2px)`,
                    width: `calc(${100 / p.columns}% - 6px)`,
                    borderLeftColor: `hsl(${hueFor(p.event.calendar)}, 60%, 50%)`,
                    background: `hsl(${hueFor(p.event.calendar)}, 45%, 22%)`,
                  }}
                  title={`${p.event.time}–${p.event.endTime} ${p.event.title} · ${p.event.calendar}${p.event.location ? `\n${p.event.location}` : ''}`}
                  onClick={() =>
                    onOpen({
                      uid: p.event.uid,
                      calendarName: p.event.calendar,
                      title: p.event.title,
                      date: p.event.date,
                      time: p.event.time,
                      endTime: p.event.endTime,
                      allDay: false,
                      location: p.event.location,
                      description: p.event.description,
                    })
                  }
                >
                  <div className="tg-event-time">
                    {p.event.time}–{p.event.endTime}
                  </div>
                  <div className="tg-event-title">{p.event.title}</div>
                  {p.height > 60 && p.event.location && <div className="tg-event-sub">{p.event.location}</div>}
                </div>
              ))}
            </div>
          );
        })}
      </div>
    </div>
  );
}
