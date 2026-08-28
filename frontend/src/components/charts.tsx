/**
 * Curves over time: the big one with axes, and the small one inside a row.
 *
 * The house had no chart before this. What was there was a bar of coloured divs (the state of
 * tickets) — enough for a proportion, nothing for a course over time. These two draw SVG and
 * take their colours from the tokens in `index.css`, because inside an SVG a Tailwind class
 * reaches nothing.
 *
 * Both draw the same picture: bands stacked on each other, the worst at the bottom. At the
 * bottom deliberately — it is the number one reads first, and a band floating on four others
 * moves whenever any of them moves, so its own shape would be the one thing invisible.
 */
import { useEffect, useRef, useState } from "react";

export type Band = { key: string; color: string };

/** One point of a curve: when, and how much per band. */
export type Point = { at: string; values: Record<string, number>; total: number };

/**
 * A round step for the value axis: 1, 2, 5, 10, 20, 50 … — never 3.7.
 */
function axisStep(max: number, wanted = 4): number {
  const raw = Math.max(1, max) / wanted;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const rest = raw / mag;
  return (rest > 5 ? 10 : rest > 2 ? 5 : rest > 1 ? 2 : 1) * mag;
}

/**
 * The label of a point in time, in the resolution the span calls for.
 *
 * Two days of runs labelled by date give six ticks reading "27.08., 27.08., 28.08." — an axis
 * that is there and says nothing. What separates the points decides what is written on them.
 */
function axisLabel(stamp: number, span: number, language: string): string {
  const d = new Date(stamp);
  if (span < 2 * 86400000) return d.toLocaleTimeString(language, { hour: "2-digit", minute: "2-digit" });
  if (span < 330 * 86400000) return d.toLocaleDateString(language, { day: "2-digit", month: "2-digit" });
  return d.toLocaleDateString(language, { month: "2-digit", year: "numeric" });
}

/** Where a point sits on the axis: by its time, not by its place in the list. */
function scale(points: Point[]) {
  const stamps = points.map((p) => new Date(p.at).getTime());
  const min = Math.min(...stamps), max = Math.max(...stamps);
  return (i: number) => (max === min ? 0.5 : (stamps[i] - min) / (max - min));
}

/** The width the element really has. Measured, not guessed — and measured again on resize. */
function useWidth<T extends HTMLElement>() {
  const ref = useRef<T | null>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    const measure = () => setWidth(node.clientWidth);
    measure();
    if (!window.ResizeObserver) return;
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    return () => observer.disconnect();
  }, []);
  return { ref, width };
}

export type Mark = { at: string; added: string[]; removed: string[] };

/**
 * The big chart: bands over time, with axes and a reading box.
 *
 * `marks` are the events between two measurements — a configuration that joined, one that
 * left. They matter: a jump in the curve has two possible causes, somebody wrote a hundred
 * findings into a stack, or a stack went away. Without the marks the curve says the first and
 * means the second.
 */
export function StackedHistory({ points, bands, marks = [], language, labelTotal, height = 210 }: {
  points: Point[];
  bands: Band[];
  marks?: Mark[];
  language: string;
  labelTotal: string;
  height?: number;
}) {
  const { ref, width } = useWidth<HTMLDivElement>();
  const [near, setNear] = useState<number | null>(null);
  if (points.length < 2) return <div ref={ref} />;

  const padL = 38, padR = 10, padT = 12, padB = 22;
  const plotW = Math.max(10, (width || 600) - padL - padR);
  const plotH = height - padT - padB;
  const max = Math.max(...points.map((p) => p.total), 1);
  const step = axisStep(max);
  const top = Math.max(step, Math.ceil(max / step) * step);
  const at = scale(points);
  const x = (i: number) => padL + at(i) * plotW;
  const y = (v: number) => padT + plotH - (v / top) * plotH;

  const ticks: number[] = [];
  for (let v = 0; v <= top + 0.001; v += step) ticks.push(v);

  // Bottom up: every band stands on the sum of the ones below it.
  let below = points.map(() => 0);
  const areas = bands.map(({ key, color }) => {
    const upper = points.map((p, i) => below[i] + (p.values[key] || 0));
    const filled = upper.some((u, i) => u > below[i]);
    const area = [
      ...points.map((_, i) => `${i ? "L" : "M"}${x(i)} ${y(upper[i])}`),
      ...points.map((_, i) => points.length - 1 - i).map((i) => `L${x(i)} ${y(below[i])}`),
      "Z",
    ].join(" ");
    const edge = points.map((_, i) => `${i ? "L" : "M"}${x(i)} ${y(upper[i])}`).join(" ");
    below = upper;
    return { key, color, area, edge, filled };
  });

  const first = new Date(points[0].at).getTime();
  const last = new Date(points[points.length - 1].at).getTime();
  const slots = Math.max(2, Math.min(6, Math.floor(plotW / 90)));
  const shown = near === null ? null : points[near];
  const mark = near === null ? null : marks[near];

  return (
    <div ref={ref} className="relative w-full">
      <svg width={width || 600} height={height} viewBox={`0 0 ${width || 600} ${height}`}
        className="block w-full"
        onMouseMove={(e) => {
          const box = e.currentTarget.getBoundingClientRect();
          const px = e.clientX - box.left;
          let best = Infinity, found = 0;
          points.forEach((_, i) => {
            const d = Math.abs(x(i) - px);
            if (d < best) { best = d; found = i; }
          });
          setNear(found);
        }}
        onMouseLeave={() => setNear(null)}>
        {ticks.map((v) => (
          <g key={v}>
            <line x1={padL} x2={padL + plotW} y1={y(v)} y2={y(v)} stroke="rgb(var(--line))" />
            <text x={padL - 6} y={y(v) + 4} textAnchor="end" fontSize="10" fill="rgb(var(--muted))">
              {Math.round(v)}
            </text>
          </g>
        ))}
        {areas.filter((a) => a.filled).map((a) => (
          <g key={a.key}>
            <path d={a.area} fill={`rgb(var(--${a.color}) / 0.55)`} />
            <path d={a.edge} fill="none" stroke={`rgb(var(--${a.color}))`} />
          </g>
        ))}
        <path d={points.map((p, i) => `${i ? "L" : "M"}${x(i)} ${y(p.total)}`).join(" ")}
          fill="none" stroke="rgb(var(--ink))" strokeWidth={1.5} />
        {points.length <= 40 && points.map((p, i) => (
          <circle key={i} cx={x(i)} cy={y(p.total)} r={2.5} fill="rgb(var(--ink))" />
        ))}
        {marks.map((m, i) => (m.added.length || m.removed.length) ? (
          <g key={i}>
            <line x1={x(i)} x2={x(i)} y1={padT} y2={padT + plotH} strokeDasharray="2 4"
              stroke={m.added.length ? "rgb(var(--grade-a))" : "rgb(var(--muted))"} />
            <path d={`M${x(i) - 4} ${padT} L${x(i) + 4} ${padT} L${x(i)} ${padT + 6} Z`}
              fill={m.added.length ? "rgb(var(--grade-a))" : "rgb(var(--muted))"} />
          </g>
        ) : null)}
        {Array.from({ length: slots + 1 }, (_, s) => {
          const stamp = first + ((last - first) * s) / slots;
          return (
            <text key={s} x={padL + (plotW * s) / slots} y={height - 6} fontSize="10"
              fill="rgb(var(--muted))"
              textAnchor={s === 0 ? "start" : s === slots ? "end" : "middle"}>
              {axisLabel(stamp, last - first, language)}
            </text>
          );
        })}
        {near !== null && (
          <line x1={x(near)} x2={x(near)} y1={padT} y2={padT + plotH}
            stroke="rgb(var(--muted))" strokeDasharray="3 3" />
        )}
      </svg>
      {shown && (
        <div className="pointer-events-none absolute top-2 whitespace-nowrap rounded-lg border
                        border-line bg-card px-3 py-2 text-xs shadow-lg"
          style={{ left: Math.max(0, Math.min((width || 600) - 170, x(near!) - 85)) }}>
          <div className="mb-1 font-medium">
            {new Date(shown.at).toLocaleString(language, {
              year: "numeric", month: "2-digit", day: "2-digit",
              hour: "2-digit", minute: "2-digit",
            })}
          </div>
          {bands.filter((b) => shown.values[b.key]).map((b) => (
            <div key={b.key} className="flex items-center gap-2">
              <i className="h-2 w-2 rounded-sm" style={{ background: `rgb(var(--${b.color}))` }} />
              <span className="capitalize">{b.key}</span>
              <b className="ml-auto tabular-nums">{shown.values[b.key]}</b>
            </div>
          ))}
          <div className="mt-1 flex items-center gap-2 border-t border-line pt-1">
            <i className="h-2 w-2 rounded-sm bg-ink" />
            <span>{labelTotal}</span>
            <b className="ml-auto tabular-nums">{shown.total}</b>
          </div>
          {!!mark?.added.length && (
            <div className="mt-1 max-w-[16rem] whitespace-normal"
              style={{ color: "rgb(var(--grade-a))" }}>+ {mark.added.join(", ")}</div>
          )}
          {!!mark?.removed.length && (
            <div className="mt-1 max-w-[16rem] whitespace-normal text-muted">
              − {mark.removed.join(", ")}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/**
 * The small curve inside a row.
 *
 * No axes, and this one may be stretched: it carries no text, and the strokes are told not to
 * scale with it. Its scale is its own — the row asks how THIS one moved, and against a
 * hundred findings elsewhere a stack going from two to four draws two hairs of equal length.
 *
 * A hole (a run this row was not part of) cuts the curve. A line drawn straight through it
 * would invent a measurement that nobody took.
 */
export function Sparkline({ points, stamps, bands, height = 30 }: {
  points: (Point | null)[];
  /** When each slot was measured — one per run, holes included. */
  stamps: string[];
  bands: Band[];
  height?: number;
}) {
  const W = 100, H = 30;
  // The axis comes from the runs and not from the points: a hole carries no time of its own,
  // and every row of the list has to stand on the same axis — otherwise the same day sits at
  // two places in two rows under each other.
  const times = stamps.map((iso) => new Date(iso).getTime());
  const min = Math.min(...times), span = Math.max(1, Math.max(...times) - min);
  const x = (i: number) => ((times[i] - min) / span) * W;
  const max = Math.max(1, ...points.map((p) => p?.total || 0));
  const y = (v: number) => H - (v / max) * (H - 2) - 1;

  const blocks: number[][] = [];
  let block: number[] = [];
  points.forEach((p, i) => {
    if (p) { block.push(i); return; }
    if (block.length) blocks.push(block);
    block = [];
  });
  if (block.length) blocks.push(block);

  return (
    <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="block w-full"
      style={{ height }}>
      {blocks.map((idx, b) => {
        const below: Record<number, number> = {};
        idx.forEach((i) => { below[i] = 0; });
        const parts = bands.map(({ key, color }) => {
          const upper: Record<number, number> = {};
          idx.forEach((i) => { upper[i] = below[i] + (points[i]!.values[key] || 0); });
          const filled = idx.some((i) => upper[i] > below[i]);
          const d = [
            ...idx.map((i, n) => `${n ? "L" : "M"}${x(i)} ${y(upper[i])}`),
            ...idx.slice().reverse().map((i) => `L${x(i)} ${y(below[i])}`),
            "Z",
          ].join(" ");
          idx.forEach((i) => { below[i] = upper[i]; });
          return filled ? <path key={key} d={d} fill={`rgb(var(--${color}) / 0.55)`} /> : null;
        });
        return (
          <g key={b}>
            {parts}
            <path d={idx.map((i, n) => `${n ? "L" : "M"}${x(i)} ${y(points[i]!.total)}`).join(" ")}
              fill="none" stroke="rgb(var(--muted))" vectorEffect="non-scaling-stroke" />
          </g>
        );
      })}
      {!blocks.length && (
        <line x1={0} x2={W} y1={H - 1} y2={H - 1} stroke="rgb(var(--line))"
          vectorEffect="non-scaling-stroke" />
      )}
    </svg>
  );
}
